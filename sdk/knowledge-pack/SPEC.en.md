# Aiming Cookie Knowledge Pack Specification (SPEC)

> 中文版：[SPEC.md](SPEC.md)
> Version: 2026-09-20 (frozen with the product v1 contract) · Applies to: `coach_knowledge_pack.v1` / `coach_knowledge_registry.v3` / `coach_mapping.v1`
> Audience: third-party authors who want to build a knowledge pack for Aiming Cookie (coaches / content creators / top-ranked players). **You do not need to know how to program** — you only write JSON text files; the product does the execution.
> Code paths referenced in this document are relative to the Aiming Cookie repository root. The validator code lives in `kovaak_tracker/coach/knowledge_pack.py` (pack level), `kovaak_tracker/coach/knowledge_registry.py` (registry level), and `kovaak_tracker/coach/mapping_rules.py` (mapping level).

---

## Table of contents

1. [Quick start: from zero to import](#1-quick-start-from-zero-to-import)
2. [Pack format and directory layout](#2-pack-format-and-directory-layout)
3. [manifest.json field by field](#3-manifestjson-field-by-field)
4. [knowledge/registry.json: knowledge-layer entry quick reference](#4-knowledgeregistryjson-knowledge-layer-entry-quick-reference)
5. [The six pack-level narrowing rules (violation codes and fixes)](#5-the-six-pack-level-narrowing-rules-violation-codes-and-fixes)
6. [mapping.json: mapping-layer rule reference (coach_mapping.v1)](#6-mappingjson-mapping-layer-rule-reference-coach_mappingv1)
7. [Install, activate, switch, and fall back: what happens when a pack is installed](#7-install-activate-switch-and-fall-back-what-happens-when-a-pack-is-installed)
8. [Import validation pipeline](#8-import-validation-pipeline)
9. [Evidence tiers: claim_level and source_level](#9-evidence-tiers-claim_level-and-source_level)
10. [scenario_prescription: the official scenario reference constraint](#10-scenario_prescription-the-official-scenario-reference-constraint)
11. [The vocabulary freeze promise](#11-the-vocabulary-freeze-promise)
12. [Validator CLI usage](#12-validator-cli-usage)
13. [Common error quick reference](#13-common-error-quick-reference)
14. [CLI output cheat-sheet](#14-cli-output-cheat-sheet)

---

## 1. Quick start: from zero to import

A piece of knowledge lives on two layers inside Aiming Cookie:

- **Knowledge layer (registry)**: for each phenomenon — what it is, why it happens, how to train it, how much, how to retest, when to stop — long-form prose.
- **Mapping layer (mapping, optional)**: "numbers to phenomenon" rules, e.g. "SPARC below -5 counts as sparc low" — only keys, numbers, and one short sentence of copy.

The full path:

```text
Step 1  Copy the sdk/knowledge-pack/template/ directory and rename it my-kb/
Step 2  Edit manifest.json: put in your own pack_id / author name / version
Step 3  Edit knowledge/registry.json: set registry_version to "<pack_id>@<pack_version>",
        replace the 3 example entries with your own (follow their field structure)
Step 4  (Optional) Edit mapping.json: adjust trigger rules; if you don't want rules,
        delete this file
Step 5  Validate locally:
        python -m kovaak_tracker.coach.knowledge_pack validate my-kb
        Seeing "OK <pack_id>@<version>" means the pack is valid
Step 6  Distribute: zip the directory and send it to your readers (one wrapper
        folder inside the zip is allowed)
Step 7  The user imports the zip in the Aiming Cookie settings page, "Knowledge
        packs" section; once validation passes they can activate it
```

The best reference is the official pack shipped in the repository: `knowledge/mapping/official.v1.json` (23 mapping rules) and the official knowledge registry (currently active version `knowledge/coach/registry.v13.json`, 118 knowledge entries). The 3 example entries in this template cover the three most commonly used capability tiers.

---

## 2. Pack format and directory layout

A pack is **one directory or one zip** containing at most four files — one extra file and the pack is rejected (error code `pack_unknown_file`):

```text
my-kb/
  manifest.json            # Required. Pack identity and compatibility declaration
  knowledge/
    registry.json          # Required. Knowledge layer (schema v3 registry)
  mapping.json             # Optional. Mapping layer; absent = pure knowledge-replacement pack
  README.md                # Optional. Reader-facing notes; never enters product context
```

**Zip packing rules**: entry paths must be relative (no absolute paths, no `..`, no backslashes — error code `zip_unsafe_entry`); one wrapper folder with a single name is allowed (e.g. right-click-compressing the whole `my-kb/` directory); an empty archive is not allowed (`pack_empty_archive`).

**Size caps** (error codes when exceeded: `pack_file_too_large` / `pack_too_large`):

| File | Cap |
|---|---|
| `manifest.json` | 64 KB |
| `knowledge/registry.json` | 1 MiB (and ≤ 512 entries) |
| `mapping.json` | 256 KB |
| `README.md` | 256 KB |
| Whole pack (zip) | 8 MiB |

A single registry prose paragraph (a section's `text`) has its own 1200-character cap — see section 4.

---

## 3. manifest.json field by field

For a complete example see `template/manifest.json`.

| Field | Required | Constraint | Notes |
|---|---|---|---|
| `schema_version` | Yes | Fixed `"coach_knowledge_pack.v1"` | Version of the pack format. Wrong value → `manifest_schema_version_invalid` |
| `pack_id` | Yes | Regex `^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$` (lowercase letters / digits / dots / hyphens; may not start or end with a dot or hyphen) | **The installation key; immutable after release.** It is also the directory name of the user data directory `DATA_ROOT/knowledge-packs/<pack_id>`, which is why dangerous values like `.` / `..` are not allowed. Error code `manifest_pack_id_invalid` |
| `display_name` | Yes | Non-empty text, ≤ 120 chars | The name shown in the settings page |
| `author` | Yes | Non-empty text, ≤ 120 chars | Author / organization name |
| `homepage` | No | Starts with `http(s)://`, ≤ 300 chars | Homepage link, display only |
| `pack_version` | Yes | semver string, e.g. `1.2.0` (may carry `-beta` / `+build` suffixes) | Your pack's version. **Every substantive content change must bump the version** (see version binding below). Error code `manifest_pack_version_invalid` |
| `license` | Yes | Non-empty text, ≤ 120 chars | License of the pack contents (e.g. `CC-BY-4.0`, `MIT`) |
| `ac_compat` | Yes | Object, see below | Compatibility declaration |

`ac_compat` fields:

| Field | Required | Constraint |
|---|---|---|
| `knowledge_schema` | Yes | Non-empty list; currently only `["coach_knowledge_registry.v3"]` is accepted |
| `mapping_schema` | Required only when the pack contains `mapping.json` | Non-empty list; currently only `["coach_mapping.v1"]` is accepted |

**Declaration and file must agree**: the pack contains `mapping.json` but declares no `mapping_schema` → `ac_compat_mapping_mismatch`; `mapping_schema` is declared but `mapping.json` is absent → also `ac_compat_mapping_mismatch`. If you don't want a mapping layer, remove both sides together.

**Version binding (hard constraint)**: the top-level `registry_version` of `knowledge/registry.json` must be exactly the string `"<pack_id>@<pack_version>"`, e.g. `com.example.my-kb@1.0.0`. The `@` keeps the version space of third-party packs forever disjoint from the official one (`2026-09-12.v12`-style date versions) — every historical analysis a user keeps records the knowledge version it used, and is replayed against exactly that version. A mismatch → `registry_version_mismatch`.

**manifest content safety** (error code `manifest_unsafe_content`): the manifest may not contain path-like text (starting with `/`, `\`, `~/`, a `drive letter:`, or `file://`), secret-like text (api key / password / bearer token, etc.), instruction-like field names (`command`, `exec`, `shell`, `prompt`, `instruction`, etc.), or sensitive field names (`apikey`, `password`, `secret`, `payload`, `rawtrace`, etc.). Pack contents may only be knowledge data; they must not carry behavioral instructions.

---

## 4. knowledge/registry.json: knowledge-layer entry quick reference

The authoritative schema is `knowledge/coach/schema.v3.json` in the repository (JSON Schema, machine-readable); the validation implementation is `kovaak_tracker/coach/knowledge_registry.py`. This section is the author-facing quick reference of common fields.

### 4.1 Top-level structure

```jsonc
{
  "schema_version": "coach_knowledge_registry.v3",   // fixed
  "registry_version": "com.example.my-kb@1.0.0",     // "<pack_id>@<pack_version>", see section 3
  "signal_aliases": { },                             // optional: your own synonyms → official signals,
                                                     // e.g. {"sparc smoothness": "sparc low"}
  "sources":  [ ... ],                               // source declarations, 1-512, duplicates not allowed
  "entries":  [ ... ]                                // knowledge entries, 1-512
}
```

### 4.2 sources[]: source declarations

Every paragraph of every entry must cite a source. This is the mechanism that governs "what a coach may claim, and with what authority".

| Field | Constraint |
|---|---|
| `source_ref` | Token format (≤ 160 chars; letters / digits / `.` `_` `:` `/` space `-`), e.g. `src.my-kb.author` |
| `source_level` | See section 9; **third-party packs may not use `product_contract` or `coach_first_party`** |
| `title` / `author_or_org` / `locator` | Text ≤ 1200 chars; `locator` says "where this source can be looked up" |
| `retrieved_at` | `YYYY-MM-DD` |
| `published_at` | Optional, ≤ 32 chars or null |
| `applicability` | The family range it applies to; `["all_families"]` is the simplest choice |
| `supports_sections` | Which section names this source may back (`definition`, `cue`, `scenario_prescription`, etc.) |

### 4.3 entries[]: common knowledge-entry fields

| Field | Constraint | Notes |
|---|---|---|
| `entry_id` | Regex `^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$` (lowercase, at least two segments), e.g. `kb.my-kb.explanation.sparc` | Entry name. By convention, entries with the `prescription.*` prefix are treated as the "training recommendation pool" and prioritized by the Coach; use this prefix for prescription-style entries |
| `entry_version` | Integer ≥ 1 | **Bump the version whenever the content changes**; only one version of an entry is live at any time |
| `status` | `"active"` or `"retired"` | Retired entries are kept for historical traceability and never take part in matching |
| `category` | Enum: `observation_definition` / `mechanism` / `training_cue` / `prescription_verification` / `limitation` / `outcome_only` | Nature of the entry |
| `topics` | 1-64 tokens | Topic tags for retrieval |
| `signals` | 0-64, **must be inside the official vocabulary `signals`** (narrowing rule 4) | Which signals this knowledge explains, e.g. `["sparc low"]` |
| `metric_refs` | 0-64, **must be inside the official vocabulary `metric_keys`** | Associated metrics, e.g. `["sparc"]` |
| `family_scope` | 1-8, enum: `static_clicking` / `dynamic_clicking` / `predictable_tracking` / `reactive_tracking` / `control_tracking` / `target_switching` / `movement_aiming` | Note: in the registry, tracking is split into three branches; there is no `continuous_tracking` value (that is a mapping-side family key) |
| `observation_refs` | 0-64 | Associated observation objects (e.g. `metric.terminal_control`). **At least 1 is required whenever the entry supports `diagnosis_support`** |
| `quality_prerequisites` | 0-64 | Data-quality prerequisites that must hold before this entry's conclusions may be cited. **At least 1 when `diagnosis_support` is supported** |
| `sources` | 1-64, must point at `source_ref` values that exist in the top-level `sources` | The entry's cited sources |
| `supported_uses` | Strict prefix ladder, see below | How deep this knowledge is allowed to be used |

**The `supported_uses` capability ladder** (exactly one of four choices; each level adds requirements; skipping levels is not allowed):

1. `["explanation_only"]` — pure explanation. **Forbidden** to carry any training field: `cue` / `dose_guardrail` / `matched_retest` / `near_transfer_retest` / `stop_adjust_rule` / `scenario_prescription`.
2. `["explanation_only", "diagnosis_support"]` — may take part in diagnosis. Training fields still forbidden; requires at least 1 `observation_refs` and 1 `quality_prerequisites` each.
3. `["explanation_only", "diagnosis_support", "candidate_experiment"]` — may suggest experiments. **Must** carry `cue` + `dose_guardrail` + `matched_retest` + `stop_adjust_rule`; `near_transfer_retest` and `scenario_prescription` forbidden.
4. `["explanation_only", "diagnosis_support", "candidate_experiment", "scenario_prescription"]` — full prescription. On top of tier 3, **must** also add `near_transfer_retest` + `scenario_prescription`.

### 4.4 section: prose fields

`definition`, `scope`, `expected_direction`, `cue`, `matched_retest`, `near_transfer_retest` are single section objects; `mechanisms`, `dose_guardrail`, `stop_adjust_rule` are section arrays (at least 1 item). `alternative_explanations` / `forbidden_inferences` / `limitations` / `counterevidence` are plain string arrays (each at least 1 item, each ≤ 500 chars).

Section structure:

```jsonc
{
  "section_ref": "kb.my-kb.explanation.sparc.definition",  // must start with "<entry_id>."
  "claim_level": "community_practice",                      // evidence tier, see section 9
  "source_refs": ["src.my-kb.author"],                      // must be a subset of the entry's sources,
                                                            // and each source's supports_sections must
                                                            // include this section name
  "text": "……"                                              // ≤ 1200 chars
}
```

`expected_direction.text` is an enum: `lower_better` / `higher_better` / `target_band` / `descriptive_only` / `comparison_only`.

**Claim ceiling**: a section's `claim_level` may not exceed the ceiling allowed by its source's `source_level`, otherwise you get `claim_level exceeds its source ceiling`. Cross-reference table:

| source_level | Highest allowed claim_level |
|---|---|
| `experimental` / `personal_experience_unverified` | `experimental` |
| `community_organization` / `coach_first_party` | `community_practice` |
| `community_consensus` | `community_consensus` |
| `academic_peer_reviewed` | `research_supported` |
| `product_contract` (forbidden for third parties) | `deterministic_rule` |

---

## 5. The six pack-level narrowing rules (violation codes and fixes)

On top of schema v3, the pack validator enforces six pack-level rules (implementation: `kovaak_tracker/coach/knowledge_pack.py`). Failing any one rejects the whole pack.

| # | Rule | Violation error code | Common cause and fix |
|---|---|---|---|
| 1 | **Scale follows v3**: ≤ 512 entries / registry ≤ 1 MiB / single prose paragraph ≤ 1200 chars | `registry_invalid`, `pack_file_too_large` | Split the pack if there are too many entries; split overlong prose into multiple mechanisms sections |
| 2 | **Source ceiling**: `sources[].source_level` may not be `product_contract` or `coach_first_party`. Third-party ceiling is `community_organization` / `community_consensus` | `source_level_forbidden` | These two levels are product/official-only; change your sources to truthful levels like `community_consensus` |
| 3 | **Prescriptions stay open, but scenario references are official-only**: `prescription.*` entries and the `scenario_prescription` capability are allowed, but `scenario_prescription.scenario_profile_ref` must hit a **reviewed** scenario of the **official** `knowledge/scenarios/registry.v1.json`. You cannot invent scenario profiles | `scenario_ref_not_official`, `scenario_registry_unavailable` | Pick reviewed entries from the official scenario registry; refs look like `scenario:static.1wall_6targets_small@1`. The scenario registry itself is never replaced by a pack |
| 4 | **Vocabulary boundary**: entry `signals` / `metric_refs` must fall inside the official vocabulary (`knowledge/mapping/vocabulary.v1.json`); `signal_aliases` may freely extend synonyms | `signal_out_of_vocabulary`, `metric_out_of_vocabulary` | Put self-invented phrasing into `signal_aliases` pointing at an official signal; metric names can only be picked from the official `metric_keys` |
| 5 | **Mapping cross-validation**: when `mapping.json` exists it must pass full `coach_mapping.v1` validation, and every `expected_entry_ref` must resolve to an **active** entry in **this pack's** registry | `mapping_invalid`, `ac_compat_mapping_mismatch` | This pulls the runtime's "unresolvable reference drops the rule" forward to an import-time error; refs are written `knowledge:<entry_id>@<entry_version>` and point at this pack's entries |
| 6 | **Unsafe-shape checks run everywhere**: path/secret regexes, field-name blacklists, depth/size caps, covering manifest, registry, mapping, and zip layout | `manifest_unsafe_content`, `zip_unsafe_entry`, `pack_unknown_file`, etc. | Pack contents may only be knowledge data and must not carry behavioral-instruction fields; do not smuggle extra files into the pack |

---

## 6. mapping.json: mapping-layer rule reference (coach_mapping.v1)

Validation and evaluation are implemented in `kovaak_tracker/coach/mapping_rules.py`. Mapping carries only **keys, numbers, and short copy** (a diagnosis sentence ≤ 600 chars); long explanatory prose must live in registry entries.

### 6.1 Top-level structure (all five keys required)

```jsonc
{
  "schema_version": "coach_mapping.v1",
  "static_clicking": [ /* StaticRule, ≤ 32 rules */ ],
  "families": {
    "continuous_tracking": [ /* FamilyRule, ≤ 32 rules */ ],
    "dynamic_clicking":    [ /* FamilyRule, ≤ 32 rules */ ],
    "target_switching":    [ /* FamilyRule, ≤ 32 rules */ ]
  },
  "archetypes":  [ /* Archetype, ≤ 32 */ ],
  "root_causes": { /* "<signal>": ["symptom copy","physical copy","training copy"], ≤ 64 keys */ }
}
```

Write empty arrays / empty objects where you have no rules, but not one of the five keys may be missing. Total rule count (static + all families) ≤ 128, file ≤ 256 KB, nesting depth ≤ 6.

### 6.2 StaticRule: static-clicking trigger rules

Evaluation input is this run's metric summary `summary` (shaped like `{metric_name: {med: median, metric_version: version}}`), an optional reference comparison `reference` (same shape), and optional `settings` (e.g. `{"cm_per_360": 23.5}`).

| Field | Required | Constraint | Notes |
|---|---|---|---|
| `signal` | Yes | Official vocabulary `signals` | The signal emitted on trigger — the "phenomenon" this rule describes |
| `severity` | Yes | `info` / `watch` / `fix` | Intended severity (see 6.7 for the current runtime finalize pass) |
| `text` | Yes | ≤ 600 chars | The diagnostic sentence (product-facing copy is Chinese today); may contain interpolation slots, see 6.7 |
| `plain_language_meaning` | No | ≤ 400 chars | Plain-language version of the phenomenon description |
| `expected_result` | No | ≤ 400 chars | Expected direction of improvement |
| `claim_level` | Yes | 5-tier enum, see section 9 | This rule's evidence tier |
| `metric_refs` | Yes | Non-empty; official vocabulary `metric_keys` | Metrics involved in the rule |
| `limitations` | No | Official vocabulary `limitation_tokens` | Limitation annotations |
| `observation_ref` | No | Official vocabulary `observation_refs` | Associated observation object |
| `conditions` | Yes | 1-4 Conditions, AND-combined | All must hold to trigger; missing data = condition not met |
| `prescriptions` | No | ≤ 8 Prescriptions | Training suggestions |

### 6.3 Condition and operators

```jsonc
{ "input": "self_summary", "metric": "decel_frac", "stat": "med", "op": ">", "value": 0.65 }
```

- `input` (value channel): `self_summary` (this run) / `reference_summary` (reference comparison, e.g. your historical baseline) / `settings` (the settings-value channel; `metric` is the bare key such as `cm_per_360`, without the `{med:...}` wrapper).
- `stat`: fixed to `med` (median) in v1; may be omitted.
- `metric`: official vocabulary `metric_keys`.

| Operator | `value` shape | Semantics (engine behavior) |
|---|---|---|
| `>` `<` `>=` `<=` | number | Compared against the channel value. **Missing channel value → condition not met** (no error, no guessing) |
| `in_band` | `[lo, hi]`, must have lo < hi | **Open interval**: holds only when lo < value < hi (boundary values do not trigger). Use `±1e308` sentinels for unbounded sides, e.g. `[60.0, 1e308]` means ">60" |
| `ratio_to_ref_lt` | positive number | `self_summary[metric].med / reference_summary[metric].med < value`. **If either side is missing or 0 → silent** (no signal is produced; never emit a misleading ratio like 0%) |
| `metric_version_not_in` | string array (≤ 16) | Holds only when the metric's `metric_version` is missing or not in the list. Use as a version gate of the form "trigger only under the old algorithm" |

### 6.4 Prescription: training suggestion

| Field | Required | Constraint |
|---|---|---|
| `scenario` | Yes | ≤ 120 chars; which scenario / method to train |
| `reason` | Yes | ≤ 400 chars; why |
| `cue` / `purpose` / `stop_or_adjust_rule` | No | ≤ 400 chars each; how to do it / why it works / when to stop |
| `retest_after` | No | ≤ 200 chars; how to retest |
| `target_metrics` / `expected_direction` | Yes | String lists (≤ 16 items; empty lists allowed, backfilled during finalize) |
| `source_level` | No | Defaults to `community_consensus` |

### 6.5 FamilyRule: candidate-observation rules for the three families (tracking / dynamic clicking / target switching)

Family rules mean something different from StaticRule: they do not do "threshold triggering" but **compare against your historical baseline** — only when the current value is worse than the baseline does a candidate observation appear, which is then attached to a matching explanation entry found in the registry.

| Field | Required | Constraint | Notes |
|---|---|---|---|
| `signal` | Yes | Vocabulary `signals` | The candidate observation's signal |
| `metric` | Yes | Vocabulary `metric_keys` (family keys, e.g. `continuous_tracking.sparc`) | The metric compared against baseline |
| `row_field` | Yes | Vocabulary `row_fields` | Which field splits "supporting rows / counter rows" (per-event evidence rows) |
| `direction` | Yes | `higher` / `lower` / `absolute_higher` | Which side counts as worse. `higher`/`lower` compare signed values, `absolute_higher` compares absolute values (for phase-type metrics); **equal to baseline does not count as worse** |
| `knowledge_metric_ref` | Yes | Vocabulary `knowledge_metric_tokens` (shaped like `metric:sparc`) | The metric token used to search the registry for explanation entries |
| `expected_entry_ref` | No | `knowledge:<entry_id>@<entry_version>` | Pin the explanation entry. **When given, it narrows + fails closed**: if the search result does not contain it → the whole rule is dropped (validated at import time to point at one of this pack's active entries) |
| `observation_ref` | Yes | Vocabulary `observation_refs` | Associated observation object |
| `requires_metric_availability` | No | Only `"available"` is accepted (the default) | The rule does not trigger when the metric is unavailable |
| `blocking_limitations` | No | Vocabulary `limitation_tokens` | **Deny list**: if the metric's own limitations intersect this list → the rule does not trigger. Use for "don't draw conclusions when the visual evidence isn't good enough" |
| `guardrails` | No | `{"all": [{"metric", "op"}]}`, `op ∈ {"<=baseline", ">=baseline"}` | Precondition gates: all must hold to continue (e.g. "only allow a smoothness conclusion if error didn't grow and time-in-radius didn't shrink"). May not gate the rule's own metric |
| `row_filter` | No | v1 only `"observable_switch_chain"` | Named row filter: take evidence only from observable target-switch chain rows; if no supporting rows remain after filtering → the rule does not trigger |
| `claim_level` | No | Defaults to `deterministic_rule` | Evidence tier |
| `requested_knowledge_sections` | No | Defaults to 7 sections: `definition`, `mechanisms`, `alternative_explanations`, `cue`, `dose_guardrail`, `matched_retest`, `stop_adjust_rule` | Which entry sections the Coach should read after the rule triggers (full selectable set is 11 sections, adding `scope`, `expected_direction`, `forbidden_inferences`, `near_transfer_retest`) |

### 6.6 Archetype and root_causes

- **Archetype** (player profile, ≤ 32): `{"id", "label", "conditions": {"<signal>": weight}, "positive"}`. The `conditions` keys are official signals and weights satisfy 0 < w ≤ 1; the product labels the player with a type by "signals → weighted profile match". `positive: true` marks the positive fallback profile (in which case `conditions` must be empty); empty conditions and `positive` must correspond one-to-one.
- **root_causes** (≤ 64 keys): keys are official signals, values are **exactly three** copy strings `[symptom layer, physical layer, training layer]`, each ≤ 400 chars. The product pulls the three-layer attribution copy for the Coach by signal.

### 6.7 Runtime engine semantics (required reading for authors, described as the current code behaves)

Passing validation is only the ticket in; at runtime there is a **hard-coded** evaluation semantics that does not change with pack contents:

1. **Missing data never triggers**. If any condition, family metric, or baseline is missing or unavailable, the rule silently skips. Better to say nothing than to say something without evidence.
2. **`text` slot interpolation**. Diagnostic sentences may contain `{slot_name:format}` placeholders (Python `str.format` style); at runtime the current values are substituted per signal, with the format spec written in your data. **The currently supported slots are fixed per signal**:
   - `decel_frac high` / `decel_frac low` → `{decel_frac_pct}`
   - `linearity high` → `{linearity_med}`
   - `sparc low` → `{sparc_med}`
   - `reverse_ratio high` → `{reverse_ratio_pct}`
   - `peak_position low` / `peak_position high` → `{peak_position_pct}`
   - `path_efficiency low` → `{path_efficiency}`
   - `peak_speed below reference` → `{self_peak}` `{ref_peak}` `{ratio_pct}`
   - `throughput below reference` → `{self_throughput}` `{ref_throughput}` `{throughput_ratio_pct}`
   - `sensitivity high` → `{cm_per_360}`
   - Other signals do not support slots (just write plain copy in `text`). **Using an unsupported slot → the rule is dropped at runtime** (the analysis continues and does not fail).
3. **A uniform calibration finalize pass for static signals**. The product currently treats all static-clicking signals as "thresholds not yet calibrated against product data": regardless of what `severity` / `claim_level` / `limitations` a static rule declares, its evaluation result is rewritten during the finalize stage (`advice._finalize_uncalibrated_findings`) to `severity=info`, `claim_level=experimental`, `limitations=[threshold_requires_product_calibration]`. **You must still write your declared fields truthfully** — the validation constraints don't change, and they only take effect in output once calibration is unlocked. Family rules' `claim_level` and similar fields are not affected by this finalize pass and are emitted as declared.
4. **Family precondition**: if that family's analysis has `support_status == "outcome_only"` this run (outcome data only, no mechanism data), or the two sides of the comparison are not comparable (`comparable != true`) → that family's candidate observations are empty.
5. **Knowledge resolution is fail-closed**: after a family rule triggers, the explanation entry must be resolved from the current registry. For rules with `expected_entry_ref`, if the resolution does not contain it → **the whole rule is dropped**; for rules without `expected_entry_ref`, if the resolution is empty → **the whole rule is dropped** (uniform semantics across the three families; a trigger without an explanation does not count as a diagnosis).
6. **`blocking_limitations` intersection → no trigger** (see 6.5).
7. **Rule-level exceptions drop only the rule**: if one rule's evaluation raises, that rule is dropped and a diagnostic is recorded; the analysis still completes.
8. **Whole-pack fallback**: while a pack is active, a missing (pure-registry pack), corrupt, or failing `mapping.json` → **fall back to the product's built-in frozen official rules**; the analysis does not fail. A family with no rules → that family falls back to the built-in rules. Note one asymmetry: a `static_clicking` array that exists but is empty yields "zero static signals" on the static path (it does not fall back to the built-in 12 rules) — authors who don't want to touch static rules should simply not ship `mapping.json`.
9. **The official pack runs on the same engine**: the official rules are data-ized as `knowledge/mapping/official.v1.json`, same format and same engine as your pack; the built-in Python rules are only a frozen fallback.

---

## 7. Install, activate, switch, and fall back: what happens when a pack is installed

Implementation: `kovaak_tracker/coach/knowledge_pack.py` (storage), `kovaak_tracker/coach/knowledge_active.py` (active resolution).

**Install layout** (under the user data directory `DATA_ROOT`):

```text
DATA_ROOT/
  config/knowledge.json          # active state (knowledge_config.v1)
  knowledge-packs/<pack_id>/     # install directory: flat overwrite; re-importing the same
                                 # pack_id overwrites in place
    manifest.json | knowledge/registry.json | mapping.json? | README.md?
```

`config/knowledge.json`:

```jsonc
{ "schema_version": "knowledge_config.v1",
  "active": "official",            // "official" or an installed pack_id
  "installed": [ { "pack_id", "pack_version", "display_name", "author",
                   "installed_at", "has_mapping" }, ... ] }
```

**Behavior from the user's point of view**:

- **What installing does**: once import passes validation, the pack lands in `knowledge-packs/<pack_id>/` and is registered in `installed`. **Installed ≠ active**: after installing, the official tier is still active by default; the user must click activate in the settings page.
- **Whole-library replacement**: when a pack is activated, the official knowledge registry and official mapping are **not loaded at all** — no stacking, no blending, no namespace isolation. The Coach's explanations, theory, and training recommendations all come from that pack alone. Only one tier can be active at a time.
- **Switching takes effect** on switch = writing `config.active` + rebuilding the materialized knowledge directory (restart the Coach session, or triggered by the app). The switch UI tells the user a Coach session restart is needed.
- **The scenario registry is always official**: scenario profiles (`knowledge/scenarios/`) are a measurement contract and are **never replaced by a pack**; your `scenario_prescription` may only reference official reviewed scenarios (section 10).
- **What happens when things break (fail-closed back to official)**: config missing / config corrupt / `active` pointing at an uninstalled pack / pack files corrupted after activation → automatic fallback to the official tier, the analysis still completes, and the fallback reason is recorded (Python side `knowledge_active.last_fallback_reason()`; sidecar side `console.error` plus a user-visible notice). **A broken pack can never cost you your analysis results.**
- **Historical analyses are unaffected**: every historical analysis records the `knowledge_registry_version` it used and is replayed against exactly that version string. After you upgrade a pack (re-importing a new `pack_version` under the same `pack_id`, overwriting the install), old analyses still point at the old version string. **Note**: v1 flat-overwrite installs do not keep old version files; once overwritten, an old version's original text cannot be re-read and historical entries are shown under a "knowledge base has been updated" label.
- **Uninstall**: deletes the install directory and unregisters the pack; if the uninstalled pack was active → automatically switches back to `official`. After uninstall, historical analyses that referenced the pack are kept, with knowledge references shown as "from a removed knowledge base" (display-only; no data is deleted).

---

## 8. Import validation pipeline

**CLI validation** (section 12) and in-product import share the same Python validation core, in this order:

```text
1. Layout check    directory/zip → file whitelist → size caps → zip path safety
2. manifest        JSON parse → fields/formats/unsafe-shape → ac_compat consistency
3. registry        read + size gate → narrowing rules 2/3/4 (source ceiling, scenario refs,
                   vocabulary) → full v3 validator pass (structure, claim ceilings,
                   fail-closed reference contracts)
                   → registry_version == "<pack_id>@<pack_version>"
4. mapping         (optional file) read → ac_compat declaration check → full coach_mapping.v1 pass
                   → cross-check: every expected_entry_ref resolves to an active entry in
                   this pack's registry
5. Verdict         any step fails → whole pack rejected, errors printed one by one as
                   {code, message, path}
```

In-product import adds two steps on top (the CLI does not do these): **TS cross-side consistency validation** (the same registry is run through the TS validator as well; both sides must accept) and **install registration** (write into `DATA_ROOT` and register config). When the official scenario registry cannot be loaded, scenario reference validation is handled fail-closed (`scenario_registry_unavailable`).

---

## 9. Evidence tiers: claim_level and source_level

Aiming Cookie's principle: **a coach may claim anything, but must declare the authority they claim it with**. The product uses this to decide how deep a piece of knowledge may be used, and the player can see the evidence grade behind every sentence.

**claim_level (the evidence tier of one conclusion, 5 tiers)**:

| Tier | Meaning |
|---|---|
| `deterministic_rule` | Deterministic rule: derived directly from measurement definitions (product/official-only tier) |
| `research_supported` | Backed by peer-reviewed research |
| `community_consensus` | Broad consensus among the community / coaching circles |
| `community_practice` | Common community practice; weaker consensus than the previous tier |
| `experimental` | Personal hypothesis / experimental claim, needs controlled-experiment validation |

**source_level (the authority tier of one source, 7 tiers)**:

| Tier | Usable by third parties? |
|---|---|
| `product_contract` | **Forbidden** (product-contract only) |
| `coach_first_party` | **Forbidden** (official first-party only) |
| `academic_peer_reviewed` | Usable (ceiling `research_supported`) |
| `community_organization` | Usable (ceiling `community_practice`) |
| `community_consensus` | Usable (ceiling `community_consensus`) |
| `personal_experience_unverified` | Usable (ceiling `experimental`) |
| `experimental` | Usable (ceiling `experimental`) |

The hard constraint is one-directional: **a section's claim_level may not exceed its source's ceiling** (cross-reference table in 4.4). A third-party author cannot promote personal experience into a deterministic rule by declaring it — that is exactly what narrowing rule 2 is for.

---

## 10. scenario_prescription: the official scenario reference constraint

`scenario_prescription` binds a training prescription to a **specific scenario profile** (the measurement contract deciding which analysis pipelines are allowed to run). Because scenario profiles directly decide "which conclusions may be generated", they belong to the product's measurement facts and **cannot be defined by third-party packs**:

- the scenario registry (`knowledge/scenarios/registry.v1.json`) is always official and is never replaced by a pack;
- in your entries, `scenario_prescription.scenario_profile_ref` may only reference official reviewed entries, in the format `scenario:<scenario entry_id>@<version>` — for example the currently reviewed official `scenario:static.1wall_6targets_small@1` (1wall 6targets small). The authoritative list is the official registry; import validation checks every reference one by one (rule 3);
- self-invented `scenario:xxx` references are rejected (`scenario_ref_not_official`).

---

## 11. The vocabulary freeze promise

The official vocabulary is `knowledge/mapping/vocabulary.v1.json` (`coach_mapping_vocabulary.v1`); mapping validation and pack narrowing both defer to it. The promise to authors has two strength levels:

**Author hard contract (append-only: nothing is renamed or deleted)**:

- `signals` — all legal signal names (e.g. `sparc low`, `tracking lag high`);
- `metric_keys` — the metric keys conditions and rules may reference (e.g. `sparc`, `continuous_tracking.sparc`);
- `observation_refs` — legal observation objects (e.g. `metric.terminal_control`, `event.switch_chain`).

Product upgrades only ever **append** to these three sections; renaming or deleting anything there is a breaking change and cannot happen without a schema major-version bump. References your pack makes inside these sets stay valid long-term.

**Product-owned (authors may reference read-only)**:

- `limitation_tokens` (limitation annotations, e.g. `threshold_requires_product_calibration`), `row_fields`, `row_classifications`, `row_filters`, `knowledge_metric_tokens`;
- `enums` (the full value sets of `severity` / `direction` / `claim_level` / `op` / `input`).

These vocabularies are owned by measurement-side product evolution; **if the product renames or removes something there, it is a breaking change and will be announced explicitly with the product version**, and the validator will then clearly point at which reference broke. Authors should avoid hard-coding these tokens into reader-facing prose.

---

## 12. Validator CLI usage

From the Aiming Cookie repository root (or any directory that can reach this repository's Python environment):

```bash
# Validate a pack directory
python -m kovaak_tracker.coach.knowledge_pack validate <pack directory or zip path>

# Windows venv environment
.venv/Scripts/python.exe -m kovaak_tracker.coach.knowledge_pack validate my-kb
```

- Exit codes: `0` = valid (prints `OK <pack_id>@<pack_version> has_mapping=<true/false>`); `1` = invalid.
- When invalid, errors are printed one per line in the format `ERROR <error code> [file path]: <reason>`, and the last line is `FAILED with N error(s)`.
- The validator only reads your pack; it never writes and never touches the network.

Examples:

```text
$ python -m kovaak_tracker.coach.knowledge_pack validate my-kb
OK com.coachwang.static-kb@1.0.0 has_mapping=True

$ python -m kovaak_tracker.coach.knowledge_pack validate my-broken-kb
ERROR signal_out_of_vocabulary [knowledge/registry.json]: entries[0].signals item '我的自定义信号' is not in the frozen official vocabulary (signals); extend signal_aliases instead
FAILED with 1 error(s)
```

---

## 13. Common error quick reference

| Error code | Meaning | Fix |
|---|---|---|
| `manifest_missing` | manifest.json is missing | Add it |
| `pack_invalid_json` | JSON syntax error | Check commas / quotes / comments with a JSON linter (JSON does not allow comments or trailing commas) |
| `pack_unknown_file` | The pack contains a file outside the whitelist | Keep only the four legal files; exclude `.DS_Store`, desktop shortcuts, etc. when packing |
| `zip_unsafe_entry` | The zip contains absolute paths or `..` | Re-compress: select the contents from inside the pack directory, or allow one wrapper folder |
| `pack_file_too_large` / `pack_too_large` | File / whole pack over the cap | See the size table in section 2 |
| `manifest_field_missing` / `manifest_field_unknown` | manifest is missing a required field / has an unknown field | Compare against the field table in section 3 |
| `manifest_pack_id_invalid` | pack_id format is illegal | Lowercase letters / digits / dots / hyphens; may not start or end with a dot or hyphen |
| `manifest_pack_version_invalid` | Version is not semver | Use three-segment form like `1.0.0` |
| `manifest_schema_version_invalid` | schema_version is wrong | Fixed value `coach_knowledge_pack.v1` |
| `ac_compat_invalid` | The ac_compat field is illegal | knowledge_schema can only be `["coach_knowledge_registry.v3"]` |
| `ac_compat_mapping_mismatch` | mapping.json and the mapping_schema declaration disagree | Sync both sides: declare if the file exists; delete the declaration if the file doesn't |
| `manifest_unsafe_content` | The manifest contains path-/secret-like text or instruction-like field names | Remove the offending content; the manifest only holds the section 3 fields |
| `registry_version_mismatch` | registry_version ≠ `<pack_id>@<pack_version>` | Sync registry_version in the registry |
| `source_level_forbidden` | An official-only source level was used | Change to a usable level like `community_consensus` (section 9) |
| `signal_out_of_vocabulary` / `metric_out_of_vocabulary` | signals / metric_refs fell outside the official vocabulary | Use official vocabulary; put self-invented synonyms into `signal_aliases` |
| `scenario_ref_not_official` | A scenario reference is not an official reviewed entry | Pick refs from the official scenario registry (section 10) |
| `scenario_registry_unavailable` | The official scenario registry could not be loaded; validation rejects fail-closed | The product installation is damaged; reinstall the product and retry |
| `registry_invalid` | The registry failed v3 validation (reason attached) | Fix per the message; compare against `knowledge/coach/schema.v3.json` and the template |
| `mapping_invalid` | The mapping failed coach_mapping.v1 validation (reason attached); includes dangling expected_entry_ref / refs pointing at retired entries | Fix per the message; expected_entry_ref must point at one of this pack's active entries |

---

## Appendix: how this spec maps to the implementation contracts

- Pack format, narrowing rules, install layout, and fallback semantics correspond to engineering contracts C1/C2/C3 (`.zcode/kb-sdk-impl-plan-2026-09-20.md` §2);
- the vocabulary freeze terms correspond to C7;
- official pack instance: `knowledge/mapping/official.v1.json`; official scenario registry: `knowledge/scenarios/registry.v1.json`;
- template pack: `sdk/knowledge-pack/template/` (guarded by automated tests; always passes validation).

---

## 14. CLI output cheat-sheet

The validator CLI (`python -m kovaak_tracker.coach.knowledge_pack validate <path>`) has a single fixed output format in v1. As shipped, the framing lines and every current message string are already **English** — there is no localization switch and no Chinese CLI output to translate. This section is therefore a plain-English map of exactly what the CLI prints, so you can read any output at a glance without digging into the validator source. It describes the code as it is (`kovaak_tracker/coach/knowledge_pack.py`); nothing in this section is normative for future versions.

### 14.1 Line grammar

| Line | When | Meaning |
|---|---|---|
| `OK <pack_id>@<pack_version> has_mapping=True\|False` | Success (exit code `0`) | The pack passed; `has_mapping` tells you whether a `mapping.json` was present |
| `ERROR <code>[ <path>]: <message>` (one per error) | Failure (exit code `1`) | One structured finding: the stable machine-readable `code`, the pack-relative file the error belongs to (omitted when not applicable), and a human-readable reason |
| `FAILED with N error(s)` | Last line on failure | Total error count; the process exits `1` |

Errors are printed in the order the pipeline in section 8 discovers them; the pack is rejected as a whole — there is no partial pass.

### 14.2 Error-code → message-pattern reference

Layout stage (directory / zip / file gates):

| Code | Message pattern |
|---|---|
| `source_not_found` | `pack source does not exist: <path>` |
| `source_invalid` | `pack source is neither a directory nor a zip: <path>` |
| `pack_invalid_zip` | `source is not a readable zip archive: <detail>` / `cannot extract <file>: <detail>` |
| `pack_empty_archive` | `zip archive contains no files` |
| `zip_unsafe_entry` | `zip entry uses an unsafe absolute path: <name>` / `zip entry escapes the pack root: <name>` |
| `pack_unknown_file` | `unexpected file in pack (allowed: knowledge/registry.json, README.md, manifest.json, mapping.json): <relative path>` |
| `manifest_missing` | `pack is missing manifest.json` |
| `registry_missing` | `pack is missing knowledge/registry.json` |
| `pack_file_unreadable` | `cannot read <file name>: <OS error>` |
| `pack_file_too_large` | `<file name> exceeds the <N> byte limit` |
| `pack_too_large` | `pack exceeds the 8388608 byte total limit` |
| `pack_invalid_json` | `<file name> is not valid JSON: <parser detail>` |

Manifest stage:

| Code | Message pattern |
|---|---|
| `manifest_invalid` | `manifest.json must be a JSON object` |
| `manifest_unsafe_content` | `manifest contains unsafe fields: <key>` / `manifest contains unsafe text` / `manifest contains overlong text` / `manifest contains unsupported value` / `manifest exceeds the nesting limit` |
| `manifest_schema_version_invalid` | `manifest schema_version must be 'coach_knowledge_pack.v1'` |
| `manifest_field_unknown` | `manifest has unknown fields: [<names>]` |
| `manifest_field_missing` | `manifest is missing required fields: [<names>]` |
| `manifest_value_invalid` | `manifest field '<name>' must be non-empty text` / `manifest field '<name>' exceeds the <N>-character limit` / `manifest field 'homepage' must be an http(s) URL of at most 300 characters` |
| `manifest_pack_id_invalid` | `manifest pack_id must match ^[a-z0-9.-]+$ and may not start or end with '.', '-' (it is the installation directory name)` |
| `manifest_pack_version_invalid` | `manifest pack_version must be a semver string like 1.2.0` |
| `ac_compat_invalid` | `manifest ac_compat must be an object with a knowledge_schema list` / `ac_compat has unknown fields: [...]` / `ac_compat is missing required fields: [...]` / `ac_compat knowledge_schema must be a non-empty list from ['coach_knowledge_registry.v3']` / `ac_compat mapping_schema must be a non-empty list from ['coach_mapping.v1']` |
| `ac_compat_mapping_mismatch` | `mapping.json is present but manifest ac_compat does not declare mapping_schema: ["coach_mapping.v1"]` / `manifest declares ac_compat.mapping_schema but mapping.json is missing` |

Registry stage (narrowing + v3 pass):

| Code | Message pattern |
|---|---|
| `vocabulary_unavailable` | `official mapping vocabulary is unavailable; cannot verify vocabulary rules` |
| `source_level_forbidden` | `sources[<i>] source_level '<level>' is first-party-only; third-party packs are capped at community_organization / community_consensus` |
| `signal_out_of_vocabulary` | `entries[<i>].signals item '<signal>' is not in the frozen official vocabulary (signals); extend signal_aliases instead` |
| `metric_out_of_vocabulary` | `entries[<i>].metric_refs item '<metric>' is not in the frozen official vocabulary (metric_keys)` |
| `scenario_registry_unavailable` | `the official scenario registry could not be loaded; cannot verify scenario_profile_ref (fail-closed)` |
| `scenario_ref_not_official` | `entries[<i>].scenario_prescription.scenario_profile_ref '<ref>' is not a reviewed scenario of the official knowledge/scenarios registry; scenario profiles cannot be defined inside a pack` |
| `registry_invalid` | `knowledge/registry.json: <reason from the v3 validator, in English>` |
| `registry_version_mismatch` | `registry registry_version must be '<pack_id>@<pack_version>' ('<pack_id>@<pack_version>'); official registries use the 'YYYY-MM-DD.vN' space and never contain '@'` |

Mapping stage:

| Code | Message pattern |
|---|---|
| `mapping_invalid` | `mapping.json: <reason from the coach_mapping.v1 validator, in English>` — typical reasons: `static_clicking[<i>].signal is not in the frozen vocabulary (signals): '<signal>'`, `families.<family>[<i>].expected_entry_ref cannot be resolved in the provided registry: <ref>`, `families.<family>[<i>].expected_entry_ref is not an active registry entry: <ref>`, `mapping is missing required fields: [...]`, `mapping contains unsafe fields: <key>` |

### 14.3 Reading tips

- The stable part of any line is the **code** — match it against the table in section 13 for the fix; the free-text message is for humans and may change between product versions.
- `registry_invalid` / `mapping_invalid` wrap a nested validator reason after the `knowledge/registry.json: ` / `mapping.json: ` prefix; the nested reason names the exact path inside the offending file (e.g. `static_clicking[2].conditions[0].value`).
- If your own pack content contains non-ASCII text (for example a Chinese signal name you invented), that text is echoed verbatim inside the error message, as in the section 12 example — the surrounding grammar is still the one documented above.
